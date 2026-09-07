/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type RecentConclusionDTO = {
    task_id: string;
    task_type: RecentConclusionDTO.task_type;
    ticker: (string | null);
    effective_trade_date: (string | null);
    completed_at: string;
    conclusion_summary?: (string | null);
    risk_flag: boolean;
    risk_hint: (string | null);
    has_report: boolean;
    updated_at: string;
};
export namespace RecentConclusionDTO {
    export enum task_type {
        SINGLE_STOCK = 'SINGLE_STOCK',
        MARKET_WIDE = 'MARKET_WIDE',
    }
}

