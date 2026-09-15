/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConceptTreeNodeDTO } from './ConceptTreeNodeDTO';
export type ConceptTreeData = {
    as_of: (string | null);
    algorithm_version: string;
    result_status: ConceptTreeData.result_status;
    items: Array<ConceptTreeNodeDTO>;
    source: (string | null);
    source_updated_at: (string | null);
    freshness_status: ConceptTreeData.freshness_status;
};
export namespace ConceptTreeData {
    export enum result_status {
        OK = 'OK',
        NO_HOT_CONCEPTS = 'NO_HOT_CONCEPTS',
    }
    export enum freshness_status {
        FRESH = 'FRESH',
        STALE = 'STALE',
    }
}

