"use client";

/**
 * The splash screen, ported from the legacy markup.
 *
 * It exists for the same reason it did before: this is a personal app opened by people who
 * are not in a hurry, and the pause is part of its manners. It dismisses on a tap and gets
 * out of the way.
 */
export function Splash({ onDismiss }: { onDismiss: () => void }) {
  return (
    <div
      id="splash"
      onClick={onDismiss}
      role="button"
      tabIndex={0}
      aria-label="Tap anywhere to begin"
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") onDismiss();
      }}
    >
      <div className="splash-logo">B</div>
      <div className="splash-title">Bella.ai</div>
      <div className="splash-tap">Tap anywhere to begin</div>
    </div>
  );
}
