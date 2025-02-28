import clsx from 'clsx'
import { BindLogic, useActions, useValues } from 'kea'
import { DateFilter } from 'lib/components/DateFilter/DateFilter'
import { SceneExport } from 'scenes/sceneTypes'

import { navigationLogic } from '~/layout/navigation/navigationLogic'
import { dataNodeCollectionLogic } from '~/queries/nodes/DataNode/dataNodeCollectionLogic'
import { Query } from '~/queries/Query/Query'
import { NodeKind } from '~/queries/schema'

import { LLM_OBSERVABILITY_DATA_COLLECTION_NODE_ID, llmObservabilityLogic, QueryTile } from './llmObservabilityLogic'

export const scene: SceneExport = {
    component: LLMObservabilityScene,
}

const Filters = (): JSX.Element => {
    const {
        dateFilter: { dateTo, dateFrom },
    } = useValues(llmObservabilityLogic)
    const { setDates } = useActions(llmObservabilityLogic)
    const { mobileLayout } = useValues(navigationLogic)

    return (
        <div
            className={clsx(
                'sticky z-20 bg-bg-3000',
                mobileLayout ? 'top-[var(--breadcrumbs-height-full)]' : 'top-[var(--breadcrumbs-height-compact)]'
            )}
        >
            <div className="border-b py-2 flex flex-row flex-wrap gap-2 md:[&>*]:grow-0 [&>*]:grow">
                <DateFilter dateFrom={dateFrom} dateTo={dateTo} onChange={setDates} />
            </div>
        </div>
    )
}

const Tiles = (): JSX.Element => {
    const { tiles } = useValues(llmObservabilityLogic)

    return (
        <div className="mt-2 grid grid-cols-1 md:grid-cols-2 xxl:grid-cols-3 gap-4">
            {tiles.map((tile, i) => (
                <QueryTileItem key={i} tile={tile} />
            ))}
        </div>
    )
}

const QueryTileItem = ({ tile }: { tile: QueryTile }): JSX.Element => {
    const { query, title, layout } = tile

    return (
        <div className={clsx('col-span-1 row-span-1 flex flex-col', layout?.className)}>
            {title && <h2 className="mb-1 flex flex-row ml-1">{title}</h2>}
            <Query query={{ kind: NodeKind.InsightVizNode, source: query }} readOnly />
        </div>
    )
}

export function LLMObservabilityScene(): JSX.Element {
    return (
        <BindLogic logic={dataNodeCollectionLogic} props={{ key: LLM_OBSERVABILITY_DATA_COLLECTION_NODE_ID }}>
            <Filters />
            <Tiles />
        </BindLogic>
    )
}
