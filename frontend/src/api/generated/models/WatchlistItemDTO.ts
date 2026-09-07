/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type WatchlistItemDTO = {
    id: string;
    watchlist_id: string;
    market: WatchlistItemDTO.market;
    symbol: string;
    display_order: number;
    created_at: string;
    updated_at: string;
};
export namespace WatchlistItemDTO {
    export enum market {
        US = 'US',
        KR = 'KR',
        CN = 'CN',
    }
}

