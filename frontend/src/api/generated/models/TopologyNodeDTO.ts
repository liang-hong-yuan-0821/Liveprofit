/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 拓扑主节点（含未执行节点）。
 */
export type TopologyNodeDTO = {
    /**
     * "{layer}:{label}"，如 "stock:Bull Researcher"
     */
    id: string;
    /**
     * 图节点显示名（含 " Analyst" 后缀），如 "Bull Researcher"、"Screening"
     */
    label: string;
    /**
     * 所属层目录名，如 "market" / "stock"
     */
    layer: string;
    /**
     * 行号（= 层执行序）：market=0、sector=1、stock=2、screening=2；screening 任务中 stock=3
     */
    row: number;
    /**
     * 层内主节点序（DFS 前序），如 stock 层 Bull Researcher=4
     */
    order: number;
    /**
     * 未执行/已执行/执行中/出错
     */
    status: TopologyNodeDTO.status;
    /**
     * 匹配到的节点日志目录数（LLM 目录 + 纯代码节点预测目录）
     */
    invocation_count: number;
    /**
     * 相对任务目录的节点目录路径，按名（seq）排序，如 ["stock/005_Bull_Researcher"]
     */
    dirs: Array<string>;
    /**
     * 是否可从该节点重跑（终态任务且 attempt 链存在 entry checkpoint；screening 任务 stock 层恒 false）
     */
    rerun_available?: boolean;
};
export namespace TopologyNodeDTO {
    /**
     * 未执行/已执行/执行中/出错
     */
    export enum status {
        NOT_EXECUTED = 'not_executed',
        EXECUTED = 'executed',
        RUNNING = 'running',
        ERROR = 'error',
    }
}

