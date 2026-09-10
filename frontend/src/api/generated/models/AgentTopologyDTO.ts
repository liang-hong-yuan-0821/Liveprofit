/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentTopologyNodeDTO } from './AgentTopologyNodeDTO';
import type { TopologyEdgeDTO } from './TopologyEdgeDTO';
/**
 * 静态全局拓扑（全层形态，build_topology 单一事实来源）。
 */
export type AgentTopologyDTO = {
    nodes: Array<AgentTopologyNodeDTO>;
    edges: Array<TopologyEdgeDTO>;
    /**
     * 组装时间
     */
    generated_at: string;
};

