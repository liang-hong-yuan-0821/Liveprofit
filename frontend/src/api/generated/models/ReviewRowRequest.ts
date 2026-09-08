/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ReviewRowRequest = {
    draft_id: number;
    action: ReviewRowRequest.action;
    event_type?: (string | null);
    event_subtype?: (string | null);
    event_condition?: (string | null);
    importance?: (number | null);
    expected_value?: (number | null);
    actual_value?: (number | null);
    previous_value?: (number | null);
    operator?: string;
};
export namespace ReviewRowRequest {
    export enum action {
        APPROVE = 'approve',
        IGNORE = 'ignore',
    }
}

