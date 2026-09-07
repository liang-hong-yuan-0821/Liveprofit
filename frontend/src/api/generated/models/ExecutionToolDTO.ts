/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionFileDTO } from './ExecutionFileDTO';
/**
 * LLM 工具调用。
 */
export type ExecutionToolDTO = {
    dir: string;
    /**
     * 工具名（req.json 的 name，缺省取目录名）
     */
    name: string;
    req: (Record<string, any> | null);
    /**
     * res.txt
     */
    res: (ExecutionFileDTO | null);
};

