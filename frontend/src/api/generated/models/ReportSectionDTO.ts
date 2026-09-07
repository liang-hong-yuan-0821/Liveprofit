/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ReportSectionDTO = {
    block: ReportSectionDTO.block;
    status: ReportSectionDTO.status;
    title?: (string | null);
    summary?: (string | null);
    content?: (string | null);
    charts?: null;
    unavailable_reason?: (string | null);
    retryable?: (boolean | null);
};
export namespace ReportSectionDTO {
    export enum block {
        MARKET = 'market',
        SECTOR = 'sector',
        STOCK = 'stock',
        DECISION = 'decision',
    }
    export enum status {
        AVAILABLE = 'AVAILABLE',
        UNAVAILABLE = 'UNAVAILABLE',
        NOT_REQUESTED = 'NOT_REQUESTED',
    }
}

