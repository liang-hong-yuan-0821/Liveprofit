/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionFileDTO } from './ExecutionFileDTO';
import type { ExecutionTushareDTO } from './ExecutionTushareDTO';
/**
 * dataprovider 调用（新格式目录或旧格式平铺 json）。
 */
export type ExecutionDpCallDTO = {
    /**
     * 新格式目录相对路径；旧格式为 null
     */
    dir?: (string | null);
    /**
     * 接口名（meta.name 或目录/文件名推导）
     */
    name: string;
    desc: (string | null);
    seq: (number | null);
    ts: (string | null);
    error: boolean;
    /**
     * 旧格式平铺文件
     */
    legacy: boolean;
    /**
     * req.json；旧格式 payload.req
     */
    req: (Record<string, any> | null);
    /**
     * res.md/res.json；旧格式 payload.res（dict → kind=json）
     */
    res: (ExecutionFileDTO | null);
    tushare: Array<ExecutionTushareDTO>;
};

