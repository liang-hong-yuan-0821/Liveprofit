/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UnavailableBlockDTO } from './UnavailableBlockDTO';
export type PendingActionDTO = {
    kind: PendingActionDTO.kind;
    task_id: string;
    task_type: PendingActionDTO.task_type;
    ticker: (string | null);
    effective_trade_date: (string | null);
    updated_at: string;
    error_code: (string | null);
    error_summary: (string | null);
    unavailable_blocks: (Array<UnavailableBlockDTO> | null);
    retryable: boolean;
};
export namespace PendingActionDTO {
    export enum kind {
        FAILED_TASK = 'FAILED_TASK',
        REPORT_SECTION_UNAVAILABLE = 'REPORT_SECTION_UNAVAILABLE',
    }
    export enum task_type {
        SINGLE_STOCK = 'SINGLE_STOCK',
        MARKET_WIDE = 'MARKET_WIDE',
    }
}

