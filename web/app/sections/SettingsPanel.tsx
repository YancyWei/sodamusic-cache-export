"use client";

import { useState } from "react";
import { Card } from "@/app/components/ui/Card";
import { Collapsible } from "@/app/components/ui/Collapsible";
import { useExportConfig } from "@/app/contexts/ExportConfigContext";
import { Wrench, CaretDown, CaretUp } from "@phosphor-icons/react";

export function SettingsPanel() {
  const [expanded, setExpanded] = useState(false);
  const config = useExportConfig();

  return (
    <Card className="p-5">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between"
      >
        <div className="flex items-center gap-2">
          <Wrench size={18} className="text-zinc-500" />
          <span className="font-medium text-zinc-900">高级选项</span>
        </div>
        {expanded ? (
          <CaretUp size={18} className="text-zinc-400" />
        ) : (
          <CaretDown size={18} className="text-zinc-400" />
        )}
      </button>

      <Collapsible open={expanded}>
        <div className="mt-4 flex flex-wrap gap-4 border-t border-zinc-100 pt-4">
          <Checkbox
            label="Dry run"
            checked={config.dryRun}
            onChange={config.setDryRun}
          />
          <Checkbox
            label="验证音频"
            checked={config.verifyAudio}
            onChange={config.setVerifyAudio}
          />
        </div>
      </Collapsible>
    </Card>
  );
}

function Checkbox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm text-zinc-700">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-zinc-300 text-emerald-600"
      />
      {label}
    </label>
  );
}
