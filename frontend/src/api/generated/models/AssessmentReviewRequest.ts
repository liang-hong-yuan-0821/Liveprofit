/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AssessmentReviewRequest = {
    expected_revision: number;
    review_status: AssessmentReviewRequest.review_status;
    labels?: (Record<string, any> | null);
    review_note: string;
};
export namespace AssessmentReviewRequest {
    export enum review_status {
        ACCEPTED = 'accepted',
        REJECTED = 'rejected',
        RETRACTED = 'retracted',
    }
}

