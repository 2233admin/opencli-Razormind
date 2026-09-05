"use client";

/*
 * SmoothUI Async Action Button
 * Source inspiration: https://smoothui.dev/r/button-copy.json
 * Copyright (c) 2024 Eduardo Calvo — MIT; see ../LICENSE.
 * Adapted to wait for real async actions and expose truthful feedback states.
 */

import { CircleAlert, Check, RefreshCw, LoaderCircle } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import styles from "./feedback.module.css";

type ActionState = "idle" | "pending" | "success" | "error";

export type AsyncActionButtonProps = {
  onAction: () => Promise<void> | void;
  label: string;
  pendingLabel?: string;
  successLabel?: string;
  errorLabel?: string;
  contextLabel?: string;
  errorHint?: string;
  idleIcon?: ReactNode;
  iconOnly?: boolean;
  disabled?: boolean;
  variant?: "default" | "outline" | "secondary" | "ghost" | "destructive";
  size?: "default" | "sm" | "icon-sm";
  className?: string;
};

const RESET_DELAY = 1600;

const AsyncActionButton = ({
  onAction,
  label,
  pendingLabel = "处理中…",
  successLabel = "已完成",
  errorLabel = "操作失败，请重试",
  contextLabel,
  errorHint,
  idleIcon = <RefreshCw className="size-4" aria-hidden="true" />,
  iconOnly = false,
  disabled = false,
  variant = "default",
  size = "default",
  className,
}: AsyncActionButtonProps) => {
  const [state, setState] = useState<ActionState>("idle");
  const stateRef = useRef<ActionState>("idle");
  const mountedRef = useRef(true);
  const actionIdRef = useRef(0);
  const actionLockRef = useRef(false);
  const resetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearResetTimer = useCallback(() => {
    if (resetTimerRef.current !== null) {
      clearTimeout(resetTimerRef.current);
      resetTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      actionIdRef.current += 1;
      clearResetTimer();
    };
  }, [clearResetTimer]);

  const runAction = useCallback(() => {
    if (disabled || actionLockRef.current || stateRef.current === "success" || stateRef.current === "pending") {
      return;
    }

    actionLockRef.current = true;
    stateRef.current = "pending";
    const actionId = ++actionIdRef.current;
    clearResetTimer();
    setState("pending");

    void Promise.resolve()
      .then(onAction)
      .then(
        () => {
          if (!mountedRef.current || actionIdRef.current !== actionId) {
            return;
          }
          actionLockRef.current = false;
          stateRef.current = "success";
          setState("success");
          resetTimerRef.current = setTimeout(() => {
            resetTimerRef.current = null;
            if (!mountedRef.current || actionIdRef.current !== actionId) {
              return;
            }
            stateRef.current = "idle";
            setState("idle");
          }, RESET_DELAY);
        },
        () => {
          if (!mountedRef.current || actionIdRef.current !== actionId) {
            return;
          }
          actionLockRef.current = false;
          stateRef.current = "error";
          setState("error");
        },
      );
  }, [clearResetTimer, disabled, onAction]);

  const labels: Record<ActionState, string> = {
    idle: label,
    pending: pendingLabel,
    success: successLabel,
    error: errorLabel,
  };
  const currentLabel = labels[state];
  const accessibleLabel = state !== "idle" && contextLabel ? `${contextLabel}：${currentLabel}` : currentLabel;
  const stateIcons: Record<ActionState, ReactNode> = {
    idle: idleIcon,
    pending: <LoaderCircle className={styles.spinner} size={16} aria-hidden="true" />,
    success: <Check size={16} aria-hidden="true" />,
    error: <CircleAlert size={16} aria-hidden="true" />,
  };
  const labelPlaceholders = (Object.entries(labels) as [ActionState, string][]).map(([labelState, text]) => (
    <span key={labelState} className={styles.labelMeasure} aria-hidden="true">
      {text}
    </span>
  ));

  return (
    <>
      <Button
        type="button"
        aria-busy={state === "pending"}
        aria-label={accessibleLabel}
        title={state === "error" && errorHint ? `${accessibleLabel}。${errorHint}` : accessibleLabel}
        className={cn("aria-disabled:cursor-not-allowed aria-disabled:opacity-50", iconOnly && "min-h-11 min-w-11", className)}
        disabled={disabled || state === "pending" || state === "success"}
        focusableWhenDisabled={state === "pending" || state === "success"}
        onClick={runAction}
        variant={variant}
        size={size}
      >
        <span className={cn(styles.content, iconOnly && styles.iconOnlyContent)}>
          {iconOnly ? null : (
            <span className={styles.labelGrid}>
              {labelPlaceholders}
              <span className={styles.currentLabel}>{currentLabel}</span>
            </span>
          )}
          <span className={styles.iconSlot} aria-hidden="true">
            {(Object.keys(stateIcons) as ActionState[]).map((iconState) => (
              <span
                key={iconState}
                className={cn(styles.iconState, state === iconState && styles.iconStateActive)}
                data-state={iconState}
              >
                {stateIcons[iconState]}
              </span>
            ))}
          </span>
        </span>
      </Button>
      <span className={styles.liveRegion} role="status" aria-atomic="true">
        {state === "idle" ? "" : `${accessibleLabel}${state === "error" && errorHint ? `。${errorHint}` : ""}`}
      </span>
    </>
  );
};

export default AsyncActionButton;
