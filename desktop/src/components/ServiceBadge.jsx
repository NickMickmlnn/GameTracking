import React from 'react';

const SERVICE_LABELS = {
  gamepass: 'Xbox Game Pass',
  psplus: 'PlayStation Plus',
  ubisoftplus: 'Ubisoft+'
};

function toTitleCase(value) {
  if (!value) {
    return '';
  }
  return value
    .toString()
    .replace(/[_-]+/g, ' ')
    .replace(/\w\S*/g, (txt) => txt.charAt(0).toUpperCase() + txt.substring(1).toLowerCase());
}

function formatPlatforms(data) {
  const labels = Array.isArray(data?.platform_labels) ? data.platform_labels.filter(Boolean) : [];
  const tokens = Array.isArray(data?.platforms) ? data.platforms.filter(Boolean) : [];

  const display = labels.length > 0 ? labels : tokens.map((token) => toTitleCase(token));

  if (!display.length) {
    return 'Platform info unavailable';
  }
  return display.join(' · ');
}

export default function ServiceBadge({ service, data, enabled }) {
  const label = SERVICE_LABELS[service] ?? service;
  const isAvailable = Boolean(data?.available);
  const className = [
    'service-badge',
    isAvailable ? 'available' : 'unavailable',
    !enabled ? 'disabled' : ''
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={className}>
      <div className="service-name">{label}</div>
      <div className="service-status">
        {isAvailable ? 'Available' : 'Not available'}
        {isAvailable && data?.tier ? ` (${data.tier})` : ''}
      </div>
      {isAvailable && (
        <div className="service-meta">
          <span className="service-platforms">{formatPlatforms(data)}</span>
        </div>
      )}
    </div>
  );
}
