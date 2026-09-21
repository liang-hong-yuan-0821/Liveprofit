/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TrendSeriesDTO } from './TrendSeriesDTO';
/**
 * 多指数趋势对比响应（/trends/{cap-tiers,boards} 两端点共用）。
 *
 * 口径差异（维护者须知）：三条板曲线是官方指数的编制口径，非"全板等权"——
 * 上证综指 2020-07-22 修订后纳入科创板、创业板指为 100 只样本股、科创50 为
 * 50 只样本股；四条分层曲线同理是指数公司样本口径。本字段组不承载口径元数据
 * （响应里只回 name 与点），口径文案在前端图注固定写明。
 */
export type TrendsData = {
    from: string;
    to: string;
    series: Array<TrendSeriesDTO>;
    as_of: (string | null);
    freshness_status: TrendsData.freshness_status;
};
export namespace TrendsData {
    export enum freshness_status {
        FRESH = 'FRESH',
        STALE = 'STALE',
        UNAVAILABLE = 'UNAVAILABLE',
    }
}

