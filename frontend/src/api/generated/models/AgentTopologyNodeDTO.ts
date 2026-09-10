/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 静态全局拓扑节点（含提示词可编辑性标注）。
 */
export type AgentTopologyNodeDTO = {
    /**
     * "{layer}:{label}"，如 "market:CN News Analyst"
     */
    id: string;
    /**
     * 图节点显示名（含 " Analyst" 后缀）
     */
    label: string;
    /**
     * 所属层目录名，如 market / sector / stock / screening
     */
    layer: string;
    /**
     * 行号（= 层执行序）
     */
    row: number;
    /**
     * 层内主节点序
     */
    order: number;
    /**
     * 是否有可编辑提示词（Screening 纯代码节点为 false）
     */
    has_prompt: boolean;
    /**
     * 是否已自定义提示词（前端标记「已自定义」）
     */
    has_override: boolean;
};

