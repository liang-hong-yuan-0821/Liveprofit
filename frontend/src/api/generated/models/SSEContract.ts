/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BusinessEventData } from './BusinessEventData';
import type { HeartbeatEventData } from './HeartbeatEventData';
import type { ResetEventData } from './ResetEventData';
/**
 * SSE 协议帧集合（OpenAPI 文档用途：运行时为 text/event-stream 分帧）。
 */
export type SSEContract = {
    business: BusinessEventData;
    reset: ResetEventData;
    heartbeat: HeartbeatEventData;
};

