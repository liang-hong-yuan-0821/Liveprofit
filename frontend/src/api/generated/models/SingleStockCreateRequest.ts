/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AnalysisOptions } from './AnalysisOptions';
export type SingleStockCreateRequest = {
    task_type: string;
    /**
     * 单股分析目标
     */
    ticker: string;
    requested_trade_date: string;
    selected_layers: Array<'market' | 'sector' | 'stock' | 'screening' | 'position'>;
    analysis_options?: (AnalysisOptions | null);
};

