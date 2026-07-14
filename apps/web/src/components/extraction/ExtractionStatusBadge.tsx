import { Tag } from "antd";

export type ExtractionStatus = "pending" | "running" | "completed" | "failed";

const COLOR_MAP: Record<ExtractionStatus, string> = {
  pending: "orange",
  running: "blue",
  completed: "green",
  failed: "red",
};

const LABEL_MAP: Record<ExtractionStatus, string> = {
  pending: "待处理",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
};

export interface ExtractionStatusBadgeProps {
  status: ExtractionStatus;
}

export function ExtractionStatusBadge({ status }: ExtractionStatusBadgeProps) {
  return <Tag color={COLOR_MAP[status]}>{LABEL_MAP[status]}</Tag>;
}

export default ExtractionStatusBadge;
