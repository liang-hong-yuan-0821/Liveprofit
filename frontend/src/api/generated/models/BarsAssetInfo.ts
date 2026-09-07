/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type BarsAssetInfo = {
    market: BarsAssetInfo.market;
    symbol: string;
    name: string;
    currency: string;
    market_timezone: string;
    supported_intervals: Array<string>;
};
export namespace BarsAssetInfo {
    export enum market {
        US = 'US',
        KR = 'KR',
        CN = 'CN',
    }
}

