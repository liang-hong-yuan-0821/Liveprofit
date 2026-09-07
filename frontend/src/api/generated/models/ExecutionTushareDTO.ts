/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionFileDTO } from './ExecutionFileDTO';
/**
 * tushare 端点子调用（DP 调用目录 tushare/ 内）。
 */
export type ExecutionTushareDTO = {
    dir: string;
    /**
     * api_name
     */
    name: string;
    seq: (number | null);
    ts: (string | null);
    probe: boolean;
    error: boolean;
    /**
     * {api_name, fields, params}
     */
    req: (Record<string, any> | null);
    /**
     * res.json（DataFrame 归一结构或 {"error":...}）
     */
    res: (ExecutionFileDTO | null);
};

