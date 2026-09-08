/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TopologyEdgeDTO } from './TopologyEdgeDTO';
import type { TopologyNodeDTO } from './TopologyNodeDTO';
export type GraphTopologyDTO = {
    task_id: string;
    attempt_no: number;
    /**
     * run 目录是否存在且可读；false 时 nodes 仍为静态结构、status 全 not_executed
     */
    available: boolean;
    /**
     * 组装时间
     */
    generated_at: string;
    nodes: Array<TopologyNodeDTO>;
    edges: Array<TopologyEdgeDTO>;
};

