/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ComputeData = {
    event_id: number;
    status: ComputeData.status;
    message?: (string | null);
};
export namespace ComputeData {
    export enum status {
        OK = 'ok',
        FAILED = 'failed',
    }
}

