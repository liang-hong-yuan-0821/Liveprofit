/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type WatchlistItemCreateRequest = {
    market: WatchlistItemCreateRequest.market;
    symbol: string;
    expected_watchlist_revision: number;
};
export namespace WatchlistItemCreateRequest {
    export enum market {
        US = 'US',
        KR = 'KR',
        CN = 'CN',
    }
}

