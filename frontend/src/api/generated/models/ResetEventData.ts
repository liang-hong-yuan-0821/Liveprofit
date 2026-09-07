/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * reset 控制帧 data（固定且仅此四字段，无 id）。
 */
export type ResetEventData = {
    task_url: string;
    earliest_event_id: string;
    occurred_at: string;
    schema_version?: string;
};

