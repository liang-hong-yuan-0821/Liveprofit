/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionNodeDTO } from './ExecutionNodeDTO';
export type ExecutionLayerDTO = {
    /**
     * "market" / "sector" / "stock" / "screening" / 未知层名
     */
    name: string;
    /**
     * 按目录名排序（seq 前缀）
     */
    nodes: Array<ExecutionNodeDTO>;
};

