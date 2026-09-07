/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HotConceptDTO } from './HotConceptDTO';
export type HotConceptsData = {
    as_of: (string | null);
    algorithm_version: string;
    result_status: HotConceptsData.result_status;
    items: Array<HotConceptDTO>;
    source: (string | null);
    source_updated_at: (string | null);
    freshness_status: HotConceptsData.freshness_status;
};
export namespace HotConceptsData {
    export enum result_status {
        OK = 'OK',
        NO_HOT_CONCEPTS = 'NO_HOT_CONCEPTS',
    }
    export enum freshness_status {
        FRESH = 'FRESH',
        STALE = 'STALE',
    }
}

