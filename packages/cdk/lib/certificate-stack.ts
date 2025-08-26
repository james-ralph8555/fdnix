import { Stack, StackProps, CfnOutput } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';

export interface FdnixCertificateStackProps extends StackProps {
  domainName: string;
}

export class FdnixCertificateStack extends Stack {
  public readonly certificate: acm.Certificate;

  constructor(scope: Construct, id: string, props: FdnixCertificateStackProps) {
    super(scope, id, props);

    const { domainName } = props;

    this.certificate = new acm.Certificate(this, 'SslCertificate', {
      domainName,
      subjectAlternativeNames: [`www.${domainName}`],
      certificateName: 'fdnix-ssl-certificate',
      validation: acm.CertificateValidation.fromDns(),
    });

    new CfnOutput(this, 'CertificateArn', {
      value: this.certificate.certificateArn,
      description: 'ARN of the ACM certificate (us-east-1)',
      exportName: 'FdnixCertificateArn',
    });
  }
}
