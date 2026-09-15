/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 概念成分股（板块概念Treemap方案 3.2）：pct_chg = as_of 当日个股涨跌幅；
 * 停牌/无行情行为 null（前端置灰）。name 缺失时以 ts_code 兜底（契约 name 必填）。
 */
export type ConceptMemberDTO = {
    ts_code: string;
    name: string;
    pct_chg: (number | null);
};

