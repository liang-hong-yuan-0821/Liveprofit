/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type MarketAssetDTO = {
    market: MarketAssetDTO.market;
    symbol: string;
    name: string;
    currency: string;
    market_timezone: string;
    display_order: number;
    enabled: boolean;
    supported_intervals: Array<string>;
    availability_status: MarketAssetDTO.availability_status;
};
export namespace MarketAssetDTO {
    export enum market {
        US = 'US',
        KR = 'KR',
        CN = 'CN',
    }
    export enum availability_status {
        AVAILABLE = 'AVAILABLE',
        DISABLED = 'DISABLED',
        UNAVAILABLE = 'UNAVAILABLE',
    }
}

