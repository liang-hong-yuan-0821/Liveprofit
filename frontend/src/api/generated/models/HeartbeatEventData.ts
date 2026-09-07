/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * heartbeat 控制帧 data（固定且仅此三字段，无 id）。
 */
export type HeartbeatEventData = {
    connection_id: string;
    sent_at: string;
    schema_version?: string;
};

