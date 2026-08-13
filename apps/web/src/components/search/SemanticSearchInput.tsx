"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Input, Segmented, Space, Typography } from "antd";

const { Text } = Typography;

type SearchMode = "text" | "semantic";

export interface SemanticSearchInputProps {
  onSearch: (query: string, mode: SearchMode) => void;
  initialMode?: SearchMode;
}

export function SemanticSearchInput({
  onSearch,
  initialMode = "text",
}: SemanticSearchInputProps) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>(initialMode);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const debouncedSearch = useCallback(
    (value: string, currentMode: SearchMode) => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
      debounceRef.current = setTimeout(() => {
        onSearch(value, currentMode);
      }, 300);
    },
    [onSearch],
  );

  // Cleanup pending timeout on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    };
  }, []);

  const handleChange = (value: string) => {
    setQuery(value);
    debouncedSearch(value, mode);
  };

  const handleModeChange = ( value: SearchMode) => {
    setMode(value);
    // Re-fire search immediately on mode switch so results reflect new mode
    debouncedSearch(query, value);
  };

  const handleSearch = (value: string) => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    onSearch(value, mode);
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="small">
      <Segmented
        value={mode}
        onChange={(val) => handleModeChange(val as SearchMode)}
        options={[
          { label: "关键词", value: "text" },
          { label: "语义", value: "semantic" },
        ]}
      />
      <Input.Search
        value={query}
        onChange={(e) => handleChange(e.target.value)}
        onSearch={handleSearch}
        placeholder={
          mode === "text" ? "输入关键词搜索" : "输入语义查询内容"
        }
        enterButton
      />
      {mode === "semantic" && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          语义搜索基于 LightRAG
        </Text>
      )}
    </Space>
  );
}

export default SemanticSearchInput;
