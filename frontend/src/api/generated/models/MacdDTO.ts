/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * MACD 副图（技术指标数据源切换方案 §3.3）：dif/dea/hist 与 bars 等长对齐。
 *
 * hist = 上游 macd_bfq 原值（≈2×(dif−dea)，上游口径，不自算）。
 */
export type MacdDTO = {
    fast: number;
    slow: number;
    signal: number;
    dif: Array<(number | null)>;
    dea: Array<(number | null)>;
    hist: Array<(number | null)>;
};

