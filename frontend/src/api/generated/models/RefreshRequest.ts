/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Resource } from './Resource';
export type RefreshRequest = {
    resources: Array<Resource>;
    mode?: RefreshRequest.mode;
};
export namespace RefreshRequest {
    export enum mode {
        AUTO = 'auto',
        RETRY = 'retry',
    }
}

