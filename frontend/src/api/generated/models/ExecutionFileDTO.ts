/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 日志内容文件（相对任务目录）。truncated 统一语义：内容未内嵌，可经 content 端点拉全量。
 */
export type ExecutionFileDTO = {
    /**
     * 相对任务目录的文件路径
     */
    path: string;
    /**
     * 按扩展名分流
     */
    kind: ExecutionFileDTO.kind;
    /**
     * kind=md/txt → str；kind=json → 解析后 dict；解析失败/超限截断时 → null
     */
    content?: (string | Record<string, any> | null);
    /**
     * json 解析失败（content=null 时区分原因）
     */
    parse_error?: boolean;
    /**
     * 超过内容上限，content=null
     */
    truncated?: boolean;
    /**
     * 原始文件字节数（截断时前端展示「查看完整内容」）
     */
    total_bytes: number;
};
export namespace ExecutionFileDTO {
    /**
     * 按扩展名分流
     */
    export enum kind {
        MD = 'md',
        JSON = 'json',
        TXT = 'txt',
    }
}

