/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BarDTO } from './BarDTO';
import type { BarsAssetInfo } from './BarsAssetInfo';
import type { IndicatorsDTO } from './IndicatorsDTO';
export type BarsData = {
    asset: BarsAssetInfo;
    interval: string;
    from: string;
    to: string;
    bars: Array<BarDTO>;
    indicators?: (IndicatorsDTO | null);
    source: (string | null);
    as_of: (string | null);
    source_updated_at: (string | null);
    freshness_status: BarsData.freshness_status;
    market_session_status: BarsData.market_session_status;
    market_closed_reason: (string | null);
};
export namespace BarsData {
    export enum freshness_status {
        FRESH = 'FRESH',
        STALE = 'STALE',
        UNAVAILABLE = 'UNAVAILABLE',
    }
    export enum market_session_status {
        OPEN = 'OPEN',
        CLOSED = 'CLOSED',
    }
}

