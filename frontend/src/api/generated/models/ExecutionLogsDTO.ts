/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionLayerDTO } from './ExecutionLayerDTO';
export type ExecutionLogsDTO = {
    task_id: string;
    attempt_no: number;
    /**
     * 任务日志目录是否存在且可读；false 时 layers 为空
     */
    available: boolean;
    /**
     * 树构建时间
     */
    generated_at: string;
    layers: Array<ExecutionLayerDTO>;
};

