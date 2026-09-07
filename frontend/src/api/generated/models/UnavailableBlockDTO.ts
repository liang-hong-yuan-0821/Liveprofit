/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UnavailableBlockDTO = {
    block: UnavailableBlockDTO.block;
    reason?: (string | null);
    retryable?: boolean;
};
export namespace UnavailableBlockDTO {
    export enum block {
        MARKET = 'market',
        SECTOR = 'sector',
        STOCK = 'stock',
        DECISION = 'decision',
    }
}

