/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PortfolioPositionDTO = {
    portfolio_id: string;
    market: PortfolioPositionDTO.market;
    symbol: string;
    quantity: number;
    average_cost: number;
    updated_at: string;
};
export namespace PortfolioPositionDTO {
    export enum market {
        US = 'US',
        KR = 'KR',
        CN = 'CN',
    }
}

