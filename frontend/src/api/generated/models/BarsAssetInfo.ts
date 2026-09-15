/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * market = 请求 market 形参回显（非表字段，instrument 无 market 列——五轮调整定稿）。
 */
export type BarsAssetInfo = {
    market: BarsAssetInfo.market;
    symbol: string;
    name: string;
};
export namespace BarsAssetInfo {
    export enum market {
        US = 'US',
        KR = 'KR',
        CN = 'CN',
    }
}

