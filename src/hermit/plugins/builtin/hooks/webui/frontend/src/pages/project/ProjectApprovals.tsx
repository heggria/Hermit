// Project Approvals tab -- reuses Approvals page pattern scoped to program.

import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ShieldAlert } from 'lucide-react';
import { ApprovalCard } from '@/components/approvals/ApprovalCard';
import { useProgramApprovals, useApproveMutation, useDenyMutation } from '@/api/hooks';
import { DataContainer } from '@/components/ui/DataContainer';
import { EmptyState } from '@/components/layout/EmptyState';
import { CardGridSkeleton } from '@/components/ui/skeletons';

export default function ProjectApprovals() {
  const { t } = useTranslation();
  const { programId } = useParams<{ programId: string }>();
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [processingAction, setProcessingAction] = useState<'approve' | 'deny' | null>(null);

  const approvalsQuery = useProgramApprovals(programId ?? '', 'pending');
  const approveMutation = useApproveMutation();
  const denyMutation = useDenyMutation();

  const approvals = approvalsQuery.data?.approvals ?? [];

  async function handleApprove(approvalId: string) {
    setProcessingId(approvalId);
    setProcessingAction('approve');
    try {
      await approveMutation.mutateAsync(approvalId);
    } finally {
      setProcessingId(null);
      setProcessingAction(null);
    }
  }

  async function handleDeny(approvalId: string, reason: string) {
    setProcessingId(approvalId);
    setProcessingAction('deny');
    try {
      await denyMutation.mutateAsync({ approvalId, reason });
    } finally {
      setProcessingId(null);
      setProcessingAction(null);
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="mx-auto w-full max-w-3xl shrink-0 px-4 pt-4 sm:px-6">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-foreground">
            {t('approvals.title')}
          </h2>
          {approvals.length > 0 && (
            <span className="inline-flex min-w-[22px] items-center justify-center rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
              {approvals.length}
            </span>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="mx-auto w-full max-w-3xl flex-1 px-4 pb-12 pt-6 sm:px-6">
        <DataContainer
          isLoading={approvalsQuery.isLoading}
          isEmpty={approvals.length === 0}
          skeleton={<CardGridSkeleton count={4} height="h-36" columns="sm:grid-cols-1 lg:grid-cols-2" />}
          emptyState={
            <EmptyState
              icon={<ShieldAlert className="size-8 text-muted-foreground/60" />}
              title={t('approvals.noApprovals')}
            />
          }
        >
          <div className="grid gap-4 sm:grid-cols-1 lg:grid-cols-2">
            {approvals.map((approval) => (
              <ApprovalCard
                key={approval.approval_id}
                approval={approval}
                onApprove={handleApprove}
                onDeny={handleDeny}
                isApproving={
                  processingId === approval.approval_id && processingAction === 'approve'
                }
                isDenying={
                  processingId === approval.approval_id && processingAction === 'deny'
                }
              />
            ))}
          </div>
        </DataContainer>
      </div>
    </div>
  );
}
