from typing import Optional
from unittest.mock import MagicMock, patch

from freezegun import freeze_time

from posthog.clickhouse.client.execute import sync_execute
from posthog.hogql.constants import LimitContext
from posthog.hogql_queries.web_analytics.web_overview import WebOverviewQueryRunner
from posthog.models import Action, Element
from posthog.models.utils import uuid7
from posthog.schema import (
    CompareFilter,
    WebOverviewQuery,
    DateRange,
    SessionTableVersion,
    HogQLQueryModifiers,
    CustomEventConversionGoal,
    ActionConversionGoal,
    BounceRatePageViewMode,
)
from posthog.settings import HOGQL_INCREASED_MAX_EXECUTION_TIME
from posthog.test.base import (
    APIBaseTest,
    ClickhouseTestMixin,
    _create_event,
    _create_person,
)


class TestWebOverviewQueryRunner(ClickhouseTestMixin, APIBaseTest):
    def _create_events(self, data, event="$pageview"):
        person_result = []
        for id, timestamps in data:
            with freeze_time(timestamps[0][0]):
                person_result.append(
                    _create_person(
                        team_id=self.team.pk,
                        distinct_ids=[id],
                        properties={
                            "name": id,
                            **({"email": "test@posthog.com"} if id == "test" else {}),
                        },
                    )
                )
            for timestamp, session_id, *extra in timestamps:
                url = None
                elements = None
                lcp_score = None
                if event == "$pageview":
                    url = extra[0] if extra else None
                elif event == "$autocapture":
                    elements = extra[0] if extra else None
                elif event == "$web_vitals":
                    lcp_score = extra[0] if extra else None
                properties = extra[1] if extra and len(extra) > 1 else {}

                _create_event(
                    team=self.team,
                    event=event,
                    distinct_id=id,
                    timestamp=timestamp,
                    properties={
                        "$session_id": session_id,
                        "$current_url": url,
                        "$web_vitals_LCP_value": lcp_score,
                        **properties,
                    },
                    elements=elements,
                )
        return person_result

    def _run_web_overview_query(
        self,
        date_from: str,
        date_to: str,
        session_table_version: SessionTableVersion = SessionTableVersion.V2,
        compare: bool = True,
        limit_context: Optional[LimitContext] = None,
        filter_test_accounts: Optional[bool] = False,
        action: Optional[Action] = None,
        custom_event: Optional[str] = None,
        includeLCPScore: Optional[bool] = False,
        bounce_rate_mode: Optional[BounceRatePageViewMode] = BounceRatePageViewMode.COUNT_PAGEVIEWS,
    ):
        modifiers = HogQLQueryModifiers(
            sessionTableVersion=session_table_version, bounceRatePageViewMode=bounce_rate_mode
        )
        query = WebOverviewQuery(
            dateRange=DateRange(date_from=date_from, date_to=date_to),
            properties=[],
            compareFilter=CompareFilter(compare=compare) if compare else None,
            modifiers=modifiers,
            filterTestAccounts=filter_test_accounts,
            conversionGoal=ActionConversionGoal(actionId=action.id)
            if action
            else CustomEventConversionGoal(customEventName=custom_event)
            if custom_event
            else None,
            includeLCPScore=includeLCPScore,
        )
        runner = WebOverviewQueryRunner(team=self.team, query=query, limit_context=limit_context)
        return runner.calculate()

    def test_no_crash_when_no_data(self):
        results = self._run_web_overview_query(
            "2023-12-08",
            "2023-12-15",
        ).results
        assert [item.key for item in results] == ["visitors", "views", "sessions", "session duration", "bounce rate"]

        results = self._run_web_overview_query(
            "2023-12-08",
            "2023-12-15",
            includeLCPScore=True,
        ).results
        assert [item.key for item in results] == [
            "visitors",
            "views",
            "sessions",
            "session duration",
            "bounce rate",
            "lcp score",
        ]

        action = Action.objects.create(
            team=self.team,
            name="Visited Foo",
            steps_json=[
                {
                    "event": "$pageview",
                    "url": "https://www.example.com/foo",
                    "url_matching": "regex",
                }
            ],
        )
        results = self._run_web_overview_query("2023-12-08", "2023-12-15", action=action).results

        assert [item.key for item in results] == [
            "visitors",
            "total conversions",
            "unique conversions",
            "conversion rate",
        ]

    def test_increase_in_users(self):
        s1a = str(uuid7("2023-12-02"))
        s1b = str(uuid7("2023-12-12"))
        s2 = str(uuid7("2023-12-11"))

        self._create_events(
            [
                ("p1", [("2023-12-02", s1a), ("2023-12-03", s1a), ("2023-12-12", s1b)]),
                ("p2", [("2023-12-11", s2)]),
            ]
        )

        results = self._run_web_overview_query(
            "2023-12-08",
            "2023-12-15",
        ).results

        visitors = results[0]
        self.assertEqual("visitors", visitors.key)
        self.assertEqual(2, visitors.value)
        self.assertEqual(1, visitors.previous)
        self.assertEqual(100, visitors.changeFromPreviousPct)

        views = results[1]
        self.assertEqual("views", views.key)
        self.assertEqual(2, views.value)
        self.assertEqual(2, views.previous)
        self.assertEqual(0, views.changeFromPreviousPct)

        sessions = results[2]
        self.assertEqual("sessions", sessions.key)
        self.assertEqual(2, sessions.value)
        self.assertEqual(1, sessions.previous)
        self.assertEqual(100, sessions.changeFromPreviousPct)

        duration_s = results[3]
        self.assertEqual("session duration", duration_s.key)
        self.assertEqual(0, duration_s.value)
        self.assertEqual(60 * 60 * 24, duration_s.previous)
        self.assertEqual(-100, duration_s.changeFromPreviousPct)

        bounce = results[4]
        self.assertEqual("bounce rate", bounce.key)
        self.assertEqual(100, bounce.value)
        self.assertEqual(0, bounce.previous)
        self.assertEqual(None, bounce.changeFromPreviousPct)

    def test_increase_in_users_using_mobile(self):
        s1a = str(uuid7("2023-12-02"))
        s1b = str(uuid7("2023-12-12"))
        s2 = str(uuid7("2023-12-11"))

        self._create_events(
            [
                ("p1", [("2023-12-02", s1a), ("2023-12-03", s1a), ("2023-12-12", s1b)]),
                ("p2", [("2023-12-11", s2)]),
            ],
            event="$screen",
        )

        results = self._run_web_overview_query(
            "2023-12-08",
            "2023-12-15",
            bounce_rate_mode=BounceRatePageViewMode.UNIQ_PAGE_SCREEN_AUTOCAPTURES,  # bounce rate won't work in the other modes
        ).results

        visitors = results[0]
        self.assertEqual("visitors", visitors.key)
        self.assertEqual(2, visitors.value)
        self.assertEqual(1, visitors.previous)
        self.assertEqual(100, visitors.changeFromPreviousPct)

        views = results[1]
        self.assertEqual("views", views.key)
        self.assertEqual(2, views.value)
        self.assertEqual(2, views.previous)
        self.assertEqual(0, views.changeFromPreviousPct)

        sessions = results[2]
        self.assertEqual("sessions", sessions.key)
        self.assertEqual(2, sessions.value)
        self.assertEqual(1, sessions.previous)
        self.assertEqual(100, sessions.changeFromPreviousPct)

        duration_s = results[3]
        self.assertEqual("session duration", duration_s.key)
        self.assertEqual(0, duration_s.value)
        self.assertEqual(60 * 60 * 24, duration_s.previous)
        self.assertEqual(-100, duration_s.changeFromPreviousPct)

        bounce = results[4]
        self.assertEqual("bounce rate", bounce.key)
        self.assertEqual(100, bounce.value)
        self.assertEqual(0, bounce.previous)
        self.assertEqual(None, bounce.changeFromPreviousPct)

    def test_all_time(self):
        s1a = str(uuid7("2023-12-02"))
        s1b = str(uuid7("2023-12-12"))
        s2 = str(uuid7("2023-12-11"))
        self._create_events(
            [
                ("p1", [("2023-12-02", s1a), ("2023-12-03", s1a), ("2023-12-12", s1b)]),
                ("p2", [("2023-12-11", s2)]),
            ]
        )

        results = self._run_web_overview_query(
            "all",
            "2023-12-15",
            compare=False,
        ).results

        visitors = results[0]
        self.assertEqual("visitors", visitors.key)
        self.assertEqual(2, visitors.value)
        self.assertEqual(None, visitors.previous)
        self.assertEqual(None, visitors.changeFromPreviousPct)

        views = results[1]
        self.assertEqual("views", views.key)
        self.assertEqual(4, views.value)
        self.assertEqual(None, views.previous)
        self.assertEqual(None, views.changeFromPreviousPct)

        sessions = results[2]
        self.assertEqual("sessions", sessions.key)
        self.assertEqual(3, sessions.value)
        self.assertEqual(None, sessions.previous)
        self.assertEqual(None, sessions.changeFromPreviousPct)

        duration_s = results[3]
        self.assertEqual("session duration", duration_s.key)
        self.assertEqual(60 * 60 * 24 / 3, duration_s.value)
        self.assertEqual(None, duration_s.previous)
        self.assertEqual(None, duration_s.changeFromPreviousPct)

        bounce = results[4]
        self.assertEqual("bounce rate", bounce.key)
        self.assertAlmostEqual(100 * 2 / 3, bounce.value)
        self.assertEqual(None, bounce.previous)
        self.assertEqual(None, bounce.changeFromPreviousPct)

    def test_comparison(self):
        s1a = str(uuid7("2023-12-02"))
        s1b = str(uuid7("2023-12-12"))
        s2 = str(uuid7("2023-12-11"))
        self._create_events(
            [
                ("p1", [("2023-12-02", s1a), ("2023-12-03", s1a), ("2023-12-12", s1b)]),
                ("p2", [("2023-12-11", s2)]),
            ]
        )

        results = self._run_web_overview_query(
            "2023-12-06",
            "2023-12-13",
            compare=True,
        ).results

        visitors = results[0]
        self.assertEqual("visitors", visitors.key)
        self.assertEqual(2, visitors.value)
        self.assertEqual(1, visitors.previous)
        self.assertEqual(100, visitors.changeFromPreviousPct)

        views = results[1]
        self.assertEqual("views", views.key)
        self.assertEqual(2, views.value)
        self.assertEqual(2, views.previous)
        self.assertEqual(0, views.changeFromPreviousPct)

        sessions = results[2]
        self.assertEqual("sessions", sessions.key)
        self.assertEqual(2, sessions.value)
        self.assertEqual(1, sessions.previous)
        self.assertEqual(100, sessions.changeFromPreviousPct)

        duration_s = results[3]
        self.assertEqual("session duration", duration_s.key)
        self.assertEqual(0, duration_s.value)
        self.assertEqual(60 * 60 * 24, duration_s.previous)
        self.assertEqual(-100, duration_s.changeFromPreviousPct)

        bounce = results[4]
        self.assertEqual("bounce rate", bounce.key)
        self.assertAlmostEqual(100, bounce.value)
        self.assertEqual(0, bounce.previous)
        self.assertEqual(None, bounce.changeFromPreviousPct)

    def test_filter_test_accounts(self):
        s1 = str(uuid7("2023-12-02"))
        # Create 1 test account
        self._create_events([("test", [("2023-12-02", s1), ("2023-12-03", s1)])])

        results = self._run_web_overview_query("2023-12-01", "2023-12-03", filter_test_accounts=True).results

        visitors = results[0]
        self.assertEqual(0, visitors.value)

        views = results[1]
        self.assertEqual(0, views.value)

        sessions = results[2]
        self.assertEqual(0, sessions.value)

        duration_s = results[3]
        self.assertEqual(None, duration_s.value)

        bounce = results[4]
        self.assertEqual("bounce rate", bounce.key)
        self.assertEqual(None, bounce.value)

    def test_dont_filter_test_accounts(self):
        s1 = str(uuid7("2023-12-02"))
        # Create 1 test account
        self._create_events([("test", [("2023-12-02", s1), ("2023-12-03", s1)])])

        results = self._run_web_overview_query("2023-12-01", "2023-12-03", filter_test_accounts=False).results

        visitors = results[0]
        self.assertEqual(1, visitors.value)

    def test_correctly_counts_pageviews_in_long_running_session(self):
        # this test is important when using the v1 sessions table as the raw sessions table will have 3 entries, one per day
        s1 = str(uuid7("2023-12-01"))
        self._create_events(
            [
                ("p1", [("2023-12-01", s1), ("2023-12-02", s1), ("2023-12-03", s1)]),
            ]
        )

        results = self._run_web_overview_query(
            "2023-12-01",
            "2023-12-03",
        ).results

        visitors = results[0]
        self.assertEqual(1, visitors.value)

        views = results[1]
        self.assertEqual(3, views.value)

        sessions = results[2]
        self.assertEqual(1, sessions.value)

    def test_conversion_goal_no_conversions(self):
        s1 = str(uuid7("2023-12-01"))
        self._create_events(
            [
                ("p1", [("2023-12-01", s1, "https://www.example.com/foo")]),
            ]
        )

        action = Action.objects.create(
            team=self.team,
            name="Visited Foo",
            steps_json=[
                {
                    "event": "$pageview",
                    "url": "https://www.example.com/bar",
                    "url_matching": "regex",
                }
            ],
        )

        results = self._run_web_overview_query("2023-12-01", "2023-12-03", action=action).results

        visitors = results[0]
        assert visitors.value == 1

        conversion = results[1]
        assert conversion.value == 0

        unique_conversions = results[2]
        assert unique_conversions.value == 0

        conversion_rate = results[3]
        assert conversion_rate.value == 0

    def test_conversion_goal_one_pageview_conversion(self):
        s1 = str(uuid7("2023-12-01"))
        self._create_events(
            [
                ("p1", [("2023-12-01", s1, "https://www.example.com/foo")]),
            ]
        )

        action = Action.objects.create(
            team=self.team,
            name="Visited Foo",
            steps_json=[
                {
                    "event": "$pageview",
                    "url": "https://www.example.com/foo",
                    "url_matching": "regex",
                }
            ],
        )

        results = self._run_web_overview_query("2023-12-01", "2023-12-03", action=action).results

        visitors = results[0]
        assert visitors.value == 1

        conversion = results[1]
        assert conversion.value == 1

        unique_conversions = results[2]
        assert unique_conversions.value == 1

        conversion_rate = results[3]
        assert conversion_rate.value == 100

    def test_conversion_goal_one_custom_event_conversion(self):
        s1 = str(uuid7("2023-12-01"))
        self._create_events(
            [
                ("p1", [("2023-12-01", s1, "https://www.example.com/foo")]),
            ],
            event="custom_event",
        )

        results = self._run_web_overview_query("2023-12-01", "2023-12-03", custom_event="custom_event").results

        visitors = results[0]
        assert visitors.value == 1

        conversion = results[1]
        assert conversion.value == 1

        unique_conversions = results[2]
        assert unique_conversions.value == 1

        conversion_rate = results[3]
        assert conversion_rate.value == 100

    def test_conversion_goal_one_custom_action_conversion(self):
        s1 = str(uuid7("2023-12-01"))
        self._create_events(
            [
                ("p1", [("2023-12-01", s1)]),
            ],
            event="custom_event",
        )

        action = Action.objects.create(
            team=self.team,
            name="Did Custom Event",
            steps_json=[
                {
                    "event": "custom_event",
                }
            ],
        )

        results = self._run_web_overview_query("2023-12-01", "2023-12-03", action=action).results

        visitors = results[0]
        assert visitors.value == 1

        conversion = results[1]
        assert conversion.value == 1

        unique_conversions = results[2]
        assert unique_conversions.value == 1

        conversion_rate = results[3]
        assert conversion_rate.value == 100

    def test_conversion_goal_one_autocapture_conversion(self):
        s1 = str(uuid7("2023-12-01"))
        self._create_events(
            [
                ("p1", [("2023-12-01", s1, [Element(nth_of_type=1, nth_child=0, tag_name="button", text="Pay $10")])]),
            ],
            event="$autocapture",
        )

        action = Action.objects.create(
            team=self.team,
            name="Paid $10",
            steps_json=[
                {
                    "event": "$autocapture",
                    "tag_name": "button",
                    "text": "Pay $10",
                }
            ],
        )

        results = self._run_web_overview_query("2023-12-01", "2023-12-03", action=action).results

        visitors = results[0]
        assert visitors.value == 1

        conversion = results[1]
        assert conversion.value == 1

        unique_conversions = results[2]
        assert unique_conversions.value == 1

        conversion_rate = results[3]
        assert conversion_rate.value == 100

    def test_conversion_rate(self):
        s1 = str(uuid7("2023-12-01"))
        s2 = str(uuid7("2023-12-01"))
        s3 = str(uuid7("2023-12-01"))

        self._create_events(
            [
                (
                    "p1",
                    [
                        ("2023-12-01", s1, "https://www.example.com/foo"),
                        ("2023-12-01", s1, "https://www.example.com/foo"),
                    ],
                ),
                (
                    "p2",
                    [
                        ("2023-12-01", s2, "https://www.example.com/foo"),
                        ("2023-12-01", s2, "https://www.example.com/bar"),
                    ],
                ),
                ("p3", [("2023-12-01", s3, "https://www.example.com/bar")]),
            ]
        )

        action = Action.objects.create(
            team=self.team,
            name="Visited Foo",
            steps_json=[
                {
                    "event": "$pageview",
                    "url": "https://www.example.com/foo",
                    "url_matching": "regex",
                }
            ],
        )

        results = self._run_web_overview_query("2023-12-01", "2023-12-03", action=action).results

        visitors = results[0]
        assert visitors.value == 3

        conversion = results[1]
        assert conversion.value == 3

        unique_conversions = results[2]
        assert unique_conversions.value == 2

        conversion_rate = results[3]
        self.assertAlmostEqual(conversion_rate.value, 100 * 2 / 3)

    def test_lcp_score(self):
        s1a = str(uuid7("2023-12-02"))
        s1b = str(uuid7("2023-12-12"))
        s2 = str(uuid7("2023-12-11"))
        s3 = str(uuid7("2023-12-11"))

        self._create_events(
            [
                (
                    "p1",
                    [
                        ("2023-12-02", s1a, "/", {"$web_vitals_LCP_value": 1000}),
                        ("2023-12-03", s1a, "/", {"$web_vitals_LCP_value": 400}),
                        ("2023-12-12", s1b, "/", {"$web_vitals_LCP_value": 320}),
                    ],
                ),
                ("p2", [("2023-12-11", s2, "/", {"$web_vitals_LCP_value": 200})]),
                ("p3", [("2023-12-11", s3, "/")]),  # no LCP value
            ],
        )

        results = self._run_web_overview_query(
            "2023-12-08",
            "2023-12-15",
            session_table_version=SessionTableVersion.V2,
            includeLCPScore=True,
        ).results

        lcp_score = results[5]
        assert lcp_score.key == "lcp score"
        assert lcp_score.value == 0.29
        assert lcp_score.previous == 1
        assert lcp_score.changeFromPreviousPct == -71

    @patch("posthog.hogql.query.sync_execute", wraps=sync_execute)
    def test_limit_is_context_aware(self, mock_sync_execute: MagicMock):
        self._run_web_overview_query("2023-12-01", "2023-12-03", limit_context=LimitContext.QUERY_ASYNC)

        mock_sync_execute.assert_called_once()
        self.assertIn(f" max_execution_time={HOGQL_INCREASED_MAX_EXECUTION_TIME},", mock_sync_execute.call_args[0][0])
