'use client';

import { linkifyText } from '@dcf/utils';

interface LinkifiedTextProps {
  text: string;
  className?: string;
}

export function LinkifiedText({ text, className = '' }: LinkifiedTextProps) {
  const segments = linkifyText(text);

  return (
    <span className={className}>
      {segments.map((segment, index) => {
        if (segment.type === 'text') {
          return <span key={index}>{segment.content}</span>;
        }

        const isTwitter = segment.linkKind === 'twitter';

        return (
          <a
            key={index}
            href={segment.href}
            target="_blank"
            rel="noopener noreferrer"
            className={
              isTwitter
                ? 'font-medium text-sky-400 underline decoration-sky-400/50 underline-offset-2 hover:text-sky-300'
                : 'text-[var(--color-accent)] underline underline-offset-2 hover:text-white'
            }
          >
            {isTwitter ? (
              <>
                <span aria-hidden className="mr-0.5">
                  𝕏
                </span>
                {segment.content}
              </>
            ) : (
              segment.content
            )}
          </a>
        );
      })}
    </span>
  );
}
