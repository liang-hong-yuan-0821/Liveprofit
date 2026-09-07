/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionDpCallDTO } from './ExecutionDpCallDTO';
import type { ExecutionFileDTO } from './ExecutionFileDTO';
import type { ExecutionToolDTO } from './ExecutionToolDTO';
/**
 * LLM 节点目录。
 */
export type ExecutionNodeDTO = {
    /**
     * 相对任务目录，如 "sector/001_Sector_News_Analyst"
     */
    dir: string;
    /**
     * 目录名数字前缀；解析失败为 null
     */
    seq: (number | null);
    /**
     * meta.json 的 node（LLM 节点名）
     */
    node: (string | null);
    /**
     * meta.json 的 model
     */
    model: (string | null);
    /**
     * meta.json 原样（含 tool_calls 摘要）
     */
    meta: (Record<string, any> | null);
    /**
     * req.md
     */
    llm_req: (ExecutionFileDTO | null);
    /**
     * res.md
     */
    llm_res: (ExecutionFileDTO | null);
    /**
     * tools/ 子目录，按名称排序
     */
    tools: Array<ExecutionToolDTO>;
    /**
     * 新格式目录 + 旧格式平铺 json，按名称排序
     */
    dp_calls: Array<ExecutionDpCallDTO>;
};

