/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 草稿保存：元数据 + 源码，一个原子请求（plan 4.2.1 风格）。
 */
export type QuantStrategyDraftUpdateRequest = {
    name: string;
    description?: (string | null);
    source_code: string;
    expected_strategy_version: number;
    expected_draft_version: number;
};

