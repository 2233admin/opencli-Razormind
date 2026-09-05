"use client";

/*
 * SmoothUI Button Copy adapter
 * Source: https://smoothui.dev/r/button-copy.json
 * Copyright (c) 2024 Eduardo Calvo — MIT; see ../LICENSE.
 */

import { Copy } from "lucide-react";
import { toast } from "sonner";

import AsyncActionButton from "@/components/smoothui/async-action-button";
import type { AsyncActionButtonProps } from "@/components/smoothui/async-action-button";

export type ButtonCopyProps = {
  text: string;
  label?: string;
  iconOnly?: boolean;
  variant?: AsyncActionButtonProps["variant"];
  size?: AsyncActionButtonProps["size"];
  className?: string;
  disabled?: boolean;
};

const ButtonCopy = ({
  text,
  label = "复制",
  iconOnly = false,
  variant = "outline",
  size = "default",
  className,
  disabled = false,
}: ButtonCopyProps) => (
  <AsyncActionButton
    key={text}
    label={label}
    pendingLabel="正在复制…"
    successLabel="已复制"
    errorLabel="复制失败，重试"
    contextLabel={label.replace(/^复制\s*/, "") || "内容"}
    errorHint="也可手动选择页面中的地址或代码进行复制"
    idleIcon={<Copy className="size-4" aria-hidden="true" />}
    iconOnly={iconOnly}
    variant={variant}
    size={size}
    className={className}
    disabled={disabled}
    onAction={async () => {
      try {
        await navigator.clipboard.writeText(text);
      } catch (error) {
        toast.error(`${label}失败，可手动选择页面中的地址或代码进行复制`);
        throw error;
      }
    }}
  />
);

export default ButtonCopy;
