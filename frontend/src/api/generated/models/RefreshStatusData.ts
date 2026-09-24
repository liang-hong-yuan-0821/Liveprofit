/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RefreshGroup } from './RefreshGroup';
export type RefreshStatusData = {
    server_time: string;
    refresh_available: boolean;
    worker_online: boolean;
    concept_display_date: (string | null);
    groups: Array<RefreshGroup>;
};

