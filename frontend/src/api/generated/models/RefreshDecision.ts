/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Resource } from './Resource';
export type RefreshDecision = {
    resource: Resource;
    decision: RefreshDecision.decision;
    job_id: (string | null);
    reason: string;
    next_retry_at: (string | null);
};
export namespace RefreshDecision {
    export enum decision {
        QUEUED = 'QUEUED',
        IN_PROGRESS = 'IN_PROGRESS',
        UP_TO_DATE = 'UP_TO_DATE',
        BLOCKED = 'BLOCKED',
        COOLDOWN = 'COOLDOWN',
    }
}

