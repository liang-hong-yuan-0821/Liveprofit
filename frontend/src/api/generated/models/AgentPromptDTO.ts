/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AgentPromptDTO = {
    /**
     * "{layer}:{label}"，如 "market:CN News Analyst"
     */
    node_id: string;
    label: string;
    layer: string;
    /**
     * 默认提示词（展示版：输出格式已展开、日期行移除）
     */
    default_prompt: string;
    /**
     * 覆盖提示词全文；无覆盖为 null
     */
    override_prompt: (string | null);
    has_override: boolean;
    /**
     * 覆盖最近更新时间；无覆盖为 null
     */
    updated_at: (string | null);
};

