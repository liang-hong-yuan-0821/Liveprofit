/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AssessmentReviewData = {
    assessment_id: string;
    event_id: number;
    fact_key: string;
    revision: number;
    review_status: AssessmentReviewData.review_status;
    task_id?: (string | null);
    task_status: AssessmentReviewData.task_status;
    idempotent_replay?: boolean;
};
export namespace AssessmentReviewData {
    export enum review_status {
        ACCEPTED = 'accepted',
        REJECTED = 'rejected',
        RETRACTED = 'retracted',
    }
    export enum task_status {
        PENDING = 'PENDING',
        QUEUED = 'QUEUED',
        RUNNING = 'RUNNING',
        RETRYING = 'RETRYING',
        SUCCEEDED = 'SUCCEEDED',
        FAILED = 'FAILED',
        CANCELLED = 'CANCELLED',
        CANCEL_REQUESTED = 'CANCEL_REQUESTED',
    }
}

