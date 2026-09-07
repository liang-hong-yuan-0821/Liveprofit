/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DataSourceDTO } from './DataSourceDTO';
import type { ReportSectionDTO } from './ReportSectionDTO';
import type { ReportTaskInfo } from './ReportTaskInfo';
export type ReportDTO = {
    schema_version: string;
    report_version: number;
    generated_at: string;
    task: ReportTaskInfo;
    sections: Array<ReportSectionDTO>;
    data_sources?: (Array<DataSourceDTO> | null);
    risk_note?: (string | null);
};

