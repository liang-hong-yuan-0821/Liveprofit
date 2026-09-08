/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ReviewRowResult = {
    draft_id: number;
    ok: boolean;
    event_id?: (number | null);
    error_code?: (string | null);
    error_message?: (string | null);
    compute_status?: ('ok' | 'failed' | 'skipped' | null);
};

