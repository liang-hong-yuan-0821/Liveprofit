/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type EnvelopeMeta = {
    /**
     * 请求标识（X-Trace-ID）
     */
    request_id: string;
    schema_version?: string;
    /**
     * 不透明续页游标；无下一页为 null
     */
    next_cursor?: (string | null);
};

