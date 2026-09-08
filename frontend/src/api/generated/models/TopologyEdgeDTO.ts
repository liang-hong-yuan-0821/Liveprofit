/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type TopologyEdgeDTO = {
    /**
     * 源节点 id，如 "stock:Bull Researcher"
     */
    source: string;
    /**
     * 目标节点 id，如 "stock:Bear Researcher"
     */
    target: string;
    /**
     * direct/conditional（条件边虚线）/loop（screening→stock 逐票循环虚线）
     */
    kind: TopologyEdgeDTO.kind;
    /**
     * 存在反向边（如 Bull↔Bear 互指），前端画弧线避免重叠
     */
    parallel: boolean;
};
export namespace TopologyEdgeDTO {
    /**
     * direct/conditional（条件边虚线）/loop（screening→stock 逐票循环虚线）
     */
    export enum kind {
        DIRECT = 'direct',
        CONDITIONAL = 'conditional',
        LOOP = 'loop',
    }
}

