/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RefreshEligibility } from './RefreshEligibility';
import type { RefreshJob } from './RefreshJob';
import type { Resource } from './Resource';
import type { WindowCoverage } from './WindowCoverage';
export type RefreshGroup = {
    expected_count: number;
    available_count: number;
    exempt_count: number;
    missing_count: number;
    resource: Resource;
    market: RefreshGroup.market;
    market_date: string;
    calendar_status: RefreshGroup.calendar_status;
    supported_through: string;
    expected_trade_date: (string | null);
    next_ready_at: (string | null);
    latest_observed_date: (string | null);
    complete_through_date: (string | null);
    freshness: RefreshGroup.freshness;
    window_coverage: WindowCoverage;
    data_version: string;
    auto_eligibility: RefreshEligibility;
    manual_eligibility: RefreshEligibility;
    job: (RefreshJob | null);
    warnings?: Array<string>;
};
export namespace RefreshGroup {
    export enum market {
        CN = 'CN',
        US = 'US',
        KR = 'KR',
    }
    export enum calendar_status {
        OK = 'OK',
        EXPIRING = 'EXPIRING',
        UNAVAILABLE = 'UNAVAILABLE',
    }
    export enum freshness {
        FRESH = 'FRESH',
        STALE = 'STALE',
        PARTIAL = 'PARTIAL',
        UNAVAILABLE = 'UNAVAILABLE',
        UNKNOWN = 'UNKNOWN',
    }
}

